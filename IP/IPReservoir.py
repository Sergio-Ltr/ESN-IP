import torch
import numpy as np
from ESN import Reservoir
import matplotlib.pyplot as plt
import torch.nn.functional as F
from IntrinsicPlasticity import IPMask 

"""

"""
class IPReservoir(Reservoir): 

    """
    
    """
    def __init__(self,  M = 1, N = 10, sparsity=0, ro_rescale = 1, W_range = (-1, 1), bias = False, bias_range = (-1,1), input_scaling=1, mask: IPMask = None):  

        # Initialize the target sample as an empty tensor, so that once a batch of pre training data comes,
        # a tensor with the same number of elements can be sampled from the target distribution.
        self.a = torch.ones(N, requires_grad = False)
        self.b = torch.zeros(N, requires_grad = False)

        super().__init__(M, N, sparsity, ro_rescale, W_range, bias, bias_range, input_scaling)
        
        if mask != None:
            self.set_IP_mask(mask)

        # To evaluate the displacement w.r.t. to the target distribution, KL divergece is the metric. 
        self.kl_log_loss = torch.nn.KLDivLoss(reduction="batchmean", log_target = True)

        # We will need to sample from the target distribution.
        self.target_sample = torch.tensor([])

        # To compute thge KL we will use the log softmax of the sample (Pytorch style). 
        self.softmax_target_sample = torch.tensor([])

        #Initialize an empty buffer where data will be further saved for statistics
        self.buffer = None


    """

    """
    def set_IP_mask(self, mask: IPMask): 
        if self.N != mask.N:
            print(f"Error. Unable to apply a mask with {mask.N} target distributions to a reservoir with {self.N} units.")
            return 
        
        self.mask = mask

    """
    
    """
    def sample_targets(self, timesteps_number, overwrite = False): 
        if self.target_sample.shape[0] == 0 or overwrite:
            self.target_sample = self.mask.sample(timesteps_number)

            if self.mask.apply_activation == False: 
                self.softmax_target_sample = F.log_softmax(self.target_sample, dim = 1)
            else: 
                self.softmax_target_sample = F.log_softmax(self.activation(self.target_sample), dim = 1)
        
        else:
            print(f"Target were already sampled for a {timesteps_number} steps long timeseries. To sample again (and eventually change samples length), set the param 'overwrite=True'.") 


    """
    
    """
    def predict(self,  U: torch.Tensor, save_gradients = False, save_states = False): 
        # Count number of timesteps to be porocessed.
        l = U.shape[0]
        output = torch.zeros((l, self.N))

        if save_states: 
            self.buffer = torch.zeros((l, self.N))

        # Start building computational graph if specified
        # such option will be used when collecting activations for batch IP. 
        if save_gradients == True: 
            self.a.requires_grad = True
            self.b.requires_grad = True

        # Iterate over each input timestamp 
        for i in range(l):
            # Useful to plot neural activity histogram            
            self.X = torch.matmul(torch.mul(U[i], self.W_u), torch.diag(self.a)) + self.b_u + torch.matmul(torch.matmul( self.Y, self.W_x), torch.diag(self.a)) + self.b_x + self.b 
            self.Y = self.activation(self.X)
            
            if save_states: 
                self.buffer[i, :] = self.X #if self.mask.apply_activation else self.X # Maybe self.Y here?? @TODO check!
            
            output[i, :] = self.Y

        return output


    """

    """
    def pre_train(self, U: torch.Tensor, eta = 0.000025, epochs = 10, transient = 100, learning_rule = "default", verbose=True, debug=False): 
        # Check if any target distribution has been defined.
        if self.mask == None: 
            print("Error: Unable to train Intrinsic Plasticity without having set any target distribution. Try setting a mask for the reservoir.")
            return 
        
        if transient != 0: 
            warm_up_applied = self.warm_up(U[0:transient], verbose)

            if warm_up_applied:
                U = U[transient:None]

        # Check if any sample has ever been collected. 
        N_sample = self.target_sample.shape[0]
        N_train = U.shape[0]

        if N_sample != N_train:
            self.sample_targets(N_train, True)

            # Save here the evolution of the KL divergence 
            self.loss_history = []
        

        if learning_rule == "default": 
            learning_rule = "online" if self.mask.areAllGaussian else "autodiff"

        if learning_rule == "online": 
            self.pre_train_online(U, eta, epochs, verbose, debug)
            return

        if learning_rule == "autodiff": 
            self.pre_train_batch(U, eta, epochs, verbose)
            return 
        
        print(f"Error: No learnin rule corresponding to '{learning_rule}'. Try using  learning_rule = 'online'  if target distributions are all Gaussian, otherwise try using  learning_rule = 'autodiff.")


    def KL(self, U: torch.Tensor):
        Y = self.predict(U, save_gradients=False)
        self.kl_value = self.kl_log_loss(F.log_softmax(Y, dim = 1), self.softmax_target_sample)
        return self.kl_value

    """

    """
    def pre_train_batch(self, U: torch.Tensor, eta = 0.000025, epochs = 10, verbose = False):
        
        for e in range(epochs):

            Y = self.predict(U, save_gradients=True)
            self.IP_loss = self.kl_log_loss(F.log_softmax(Y, dim = 1), self.softmax_target_sample)
            self.kl_value = self.IP_loss.detach().flatten()
            self.loss_history.append(self.kl_value)

            self.IP_loss.backward(create_graph=True)

            self.a = (self.a - torch.mul(eta, self.a.grad)).detach()
            self.b = (self.b - torch.mul(eta, self.b.grad )).detach()
            
            self.a.grad = None
            self.b.grad = None

            self.a.requires_grad = False
            self.a.requires_grad = False


            if verbose: 
                print(f"- Epoch: {e + 1}) | KL Divergence value: {self.IP_loss}.")


    """"
    
    """
    def pre_train_online(self, U, eta = 0.000025,  epochs = 10, verbose=False, debug=False):
        if self.mask.areAllGaussian == False:
            print("WARNING: Only target  Gaussian distributions can be learned online. Use batch IP.")
            return 
        
        mu = self.mask.means()
        sigma = self.mask.stds()

        square_sigma = torch.mul(sigma, sigma)

        self.a_history = [] 
        self.b_history = []

        for e in range(epochs):
            i = 0
            # Iterate over each timestep of the input timeseries
            for U_t in U:
                # Fed the reservoir withn the current timestep of the input timeseries, 
                # in order to update the internal states X and Y before applying the online learnin rule. 
                self.predict(torch.tensor([U_t]),False,False)
                
                if debug: 
                    i += 1
                    print(f"------------------- Timestep: {i} ----------------------")
                    print(f"a:{self.a} - b: {self.b} - U: {U_t} - X: {self.X} - Y:{self.Y}")

                summation = 2 * square_sigma - 1 - torch.mul(self.Y, self.Y) + torch.mul(mu, self.Y)

                delta_b = - torch.mul(eta, (torch.div(- mu, square_sigma)) + torch.mul(torch.div(self.Y, square_sigma), summation))
                delta_a = torch.div(eta, self.a) + torch.mul(delta_b, self.X) 

                self.b += delta_b.reshape((self.N))
                self.a += delta_a.reshape((self.N))

                self.a_history.append([self.a, delta_a])         
                self.b_history.append([self.b, delta_b])

                if debug: 
                    print(f"a:{self.a} - b: {self.b} -  X: {self.X} - Y:{self.Y}")
                    print(f"Summation:{summation} - De_a: {delta_a} - De_b: {delta_b}")

            self.IP_loss = self.kl_log_loss(F.log_softmax(self.predict(U), dim = 1), self.softmax_target_sample)
            self.kl_value = self.IP_loss.detach().flatten()
            self.loss_history.append(self.kl_value)

            if verbose: 
                print(f"- Epoch: {e + 1}) | KL Divergence value: {self.IP_loss}. | Spectral radius: {self.max_eigs()}")
   
    def LCE(self, U): 
        return super().LCE(U, self.a)

    """

    """
    def shape(self):
        # Call parent method
        super().shape(self)

        # Also print shapes of the IP parameters. 
        print("IP gain", self.a.shape )
        print("IP bias", self.b.shape )


    """

    """
    def print_IP_stats(self, units = []):
        if self.target_sample.shape[0] == 0:
            print("Nothing to print - Reservoir not pretrained yet.")
            return 
        
        if self.buffer == None: 
            print("Nothing to print - No activation saved in the buffer")
            return 
        
        for i in range(self.N) if len(units) == 0 else units:
            actual_std, actual_mean = torch.std_mean(self.buffer[:,i] )
            target_std, target_mean = torch.std_mean(self.target_sample[:,i])
            print(f"Unit - ({i + 1}): [ ACTUAL_MEAN == ({actual_mean})  ACTUAL_STD == ({actual_std})][ TARGET_MEAN == ({target_mean}) TARGET_STD == ({target_std})]")

        actual_std, actual_mean = torch.std_mean(self.buffer)
        print(f"Overall network: [ACTUAL_MEAN == ({actual_mean})  ACTUAL_STD == ({actual_std})]")


    """

    """
    def printLossCurve(self): 
        #@TODO implement. 
        return self.loss_history
    

    """

    """
    def print_eigs(self, scaled = True):
        if not scaled:
            print("Eigenvalues of the non scaled weights") 
            return super().print_eigs()
        else:
            print("Eigenvalues of the scaled weights")
            return torch.view_as_real(torch.linalg.eigvals(torch.matmul(self.W_x,  torch.diag(self.a))))


    def max_eigs(self,  scaled = True):
        if not scaled:
            return super().max_eigs()
        else:
            return max(abs(torch.linalg.eigvals((torch.matmul(self.W_x,  torch.diag(self.a))))))
        
    
    def rescale_weights(self, ro_rescale = 0.96, scale_ip =True, verbose = False): 
        if verbose:
          print(f"Rescaling reccurent weight from their current spetral radius of {self.max_eigs()} to {ro_rescale}")
        self.W_x = (self.W_x/self.max_eigs(scale_ip) ) * ro_rescale

    """
    
    """
    def plot_local_neural_activity(self, units = []):        
        if self.buffer == None: 
            print("Nothing to print - No activation saved in the buffer")
            return 

        for i in range(self.buffer.shape[1]) if len(units) == 0 else units:
            x = self.buffer[:,i]

            x = x.detach().numpy()
            y = self.target_sample[:,i].detach().numpy()

            xs = np.linspace(y.min(), y.max(), 500)
            ys = np.zeros_like(xs)

            #plt.set_title(f"Activations of neuron {i+1}")
            plt.plot(xs, ys)
            plt.hist([x, y], bins="fd", label=['Actual', 'Target'])
            plt.show()


    """
    
    """
    def plot_global_neural_activity(self, apply_activation_X = True, apply_activation_Y=False):
        if self.buffer == None: 
            print("Nothing to print - No activation saved in the buffer")
            return 
        
        x = self.buffer
        y = self.target_sample

        if apply_activation_X: 
            x = self.activation(x)

        if apply_activation_Y: 
            y = self.activation(y)

        x = x.flatten().detach().numpy()
        y = y.flatten().detach().numpy()

        xs = np.linspace(y.min(), y.max(), 500)
        ys = np.zeros_like(xs)

        #plt.set_title(f"Activations of neuron {i+1}")
        plt.plot(xs, ys)
        plt.hist([x, y],  bins="fd", label=['Actual', 'Target'])
        plt.xlabel("x")
        plt.ylabel("f(x)")
        plt.show()

    #
    def safe_mode_pre_train(self, U: torch.Tensor, eta = 0.000025, max_epochs = 25, max_rho =1,  transient = 100, learning_rule = "default", verbose=True, debug=False): 
        prev_kl = float('inf')
        rho = 0
        rolled_back = False
        poor_learning = False

        for i in range(max_epochs):
            # Check ESC to decide wheather to use a positive or negative eta 
            if verbose:
                print(f"Epoch: {i}) - Safe mode traning - Learning Rate = {eta}")

            self.pre_train(U, eta = eta , epochs = 1, transient = transient, learning_rule=learning_rule, verbose=verbose, debug=debug)
            rho_i = self.max_eigs()
            KL_i = self.IP_loss.item()

            if rho_i >= max_rho: # Rollback!
                if verbose: 
                    print(f"Too high spectral radius: {rho_i}, rolling back to previous state!")

                self.pre_train(U, eta = -eta*1.25, epochs = 1, transient = transient, learning_rule=learning_rule, verbose=verbose, debug=debug)
                rolled_back = True
                eta = eta/4
            else:
                delta_rho = rho - rho_i 
                delta_KL = prev_kl - KL_i if prev_kl != float('inf') else KL_i

                if (abs(delta_rho) < 0.005) or (abs(delta_KL) < 0.005):

                    if (rolled_back and poor_learning):
                        if verbose: 
                            print(f"Algorithm stopped for poor learning. Spectral radius variation {delta_rho}. KL variation: {delta_KL}")
                        break

                    if (poor_learning): 
                        eta = 2*eta
                        if verbose: 
                            print(f"Algorithm learning very slowly. Spectral radius variation {delta_rho}. KL variation: {delta_KL}")
                            print(f"Trying doubling eta: {eta}")
    
                    poor_learning = True

                else:
                    # Otherwise, if everything is stable and learning is still happening, simply iterate!        
                    poor_learning = False
                    rolled_back = False

        